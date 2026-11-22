"""
scheduler_service.py
====================
Background scheduler (APScheduler 3.x).

The database is the single source of truth. APScheduler only keeps
an in-memory copy of the jobs, which is rebuilt from the database
every time the application starts.

One schedule = one or more "slots" (weekday + HH:MM).
Each slot becomes one CronTrigger job in the schedule's time zone.

    Everyday  -> 7 slots, same time
    Weekdays  -> mon-fri, same time
    Weekends  -> sat-sun, same time
    Custom    -> any days, a different time per day
"""

import logging
import re
from zoneinfo import ZoneInfo, available_timezones

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

import report_store as store
from report_service import run_report_job
from gmail_service import send_report_email

log = logging.getLogger("scheduler")

DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
DAY_LABELS = [
    "Monday", "Tuesday", "Wednesday", "Thursday",
    "Friday", "Saturday", "Sunday",
]
RECURRENCES = ["Everyday", "Weekdays", "Weekends", "Custom"]

TIMEZONES = sorted(available_timezones()) or ["UTC", "Europe/Berlin"]

DEFAULT_EMAIL_BODY = (
    "Hello,\n\n"
    "Please find attached the SAP Inventory Intelligence report "
    "generated from the latest inventory data.\n\n"
    "Regards"
)

_TIME_RE = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")

scheduler = BackgroundScheduler(
    job_defaults={
        "coalesce": True,            # missed runs collapse into one
        "max_instances": 1,          # never overlap the same job
        "misfire_grace_time": 900,   # still run if up to 15 min late
    }
)


# =========================================================
# VALIDATION / SLOT BUILDING
# =========================================================

def normalize_time(value):
    """'8:00' / '08:00' -> '08:00'. Raises ValueError if invalid."""
    match = _TIME_RE.match((value or "").strip())

    if not match:
        raise ValueError(
            f"'{value}' is not a valid time. Use 24-hour HH:MM, "
            "for example 08:00 or 16:30."
        )

    return f"{int(match.group(1)):02d}:{match.group(2)}"


def build_slots(recurrence, simple_time, day_enabled, day_times):
    """
    Convert the form inputs into a list of
    {"day": "mon", "time": "08:00"} slots.
    """

    if recurrence == "Custom":
        slots = []

        for day, enabled, time_value in zip(DAYS, day_enabled, day_times):
            if enabled:
                slots.append({
                    "day": day,
                    "time": normalize_time(time_value),
                })

        if not slots:
            raise ValueError(
                "Custom schedule: select at least one weekday."
            )

        return slots

    selected_days = {
        "Everyday": DAYS,
        "Weekdays": DAYS[:5],
        "Weekends": DAYS[5:],
    }.get(recurrence)

    if selected_days is None:
        raise ValueError(f"Unknown recurrence: {recurrence}")

    time_value = normalize_time(simple_time)

    return [{"day": d, "time": time_value} for d in selected_days]


def describe_slots(recurrence, slots):
    """Short text for the schedules table."""
    times = sorted({s["time"] for s in slots})

    if recurrence != "Custom":
        return ", ".join(times)

    parts = []

    for slot in slots:
        label = DAY_LABELS[DAYS.index(slot["day"])][:3]
        parts.append(f"{label} {slot['time']}")

    return "; ".join(parts)


# =========================================================
# JOB REGISTRATION
# =========================================================

def _job_prefix(schedule_id):
    return f"{schedule_id}:"


def unregister_schedule(schedule_id):
    for job in scheduler.get_jobs():
        if job.id.startswith(_job_prefix(schedule_id)):
            job.remove()


def register_schedule(schedule):
    """(Re)create the cron jobs for one schedule row."""

    unregister_schedule(schedule["schedule_id"])

    if schedule["status"] != "active":
        return

    tz = ZoneInfo(schedule["timezone"])

    for slot in schedule["slots"]:
        hour, minute = slot["time"].split(":")

        scheduler.add_job(
            execute_schedule,
            trigger=CronTrigger(
                day_of_week=slot["day"],
                hour=int(hour),
                minute=int(minute),
                timezone=tz,
            ),
            args=[schedule["schedule_id"]],
            id=f"{schedule['schedule_id']}:{slot['day']}:{slot['time']}",
            replace_existing=True,
        )


def next_run_text(schedule_id):
    """Earliest upcoming run of a schedule, in its own time zone."""
    times = [
        job.next_run_time
        for job in scheduler.get_jobs()
        if job.id.startswith(_job_prefix(schedule_id))
        and job.next_run_time
    ]

    if not times:
        return ""

    return min(times).strftime("%a %d %b %Y %H:%M")


# =========================================================
# EXECUTION  (runs in a scheduler worker thread)
# =========================================================

def execute_schedule(schedule_id, triggered_manually=False):
    schedule = store.get_schedule(schedule_id)

    if not schedule or (
        schedule["status"] != "active" and not triggered_manually
    ):
        return

    log.info("Running schedule %s (%s)", schedule_id, schedule["name"])

    # 1-4: agent -> BigQuery -> report -> persistent file + DB record
    try:
        result = run_report_job(
            question=schedule["question"],
            report_format=schedule["report_format"],
            name=schedule["name"],
            report_type="Scheduled",
            schedule_id=schedule_id,
            include_vendor_check=bool(
                schedule.get("include_vendor_check")
            ),
        )

    except Exception as exc:
        log.exception("Schedule %s: report generation failed", schedule_id)

        store.update_schedule(
            schedule_id,
            last_run_at=store.now_iso(),
            last_status=f"Failed: {exc}"[:300],
        )
        return

    report_id = result["report_id"]

    # 5: e-mail to all recipients
    store.update_report(report_id, email_status="Sending")

    try:
        send_report_email(
            recipient=schedule["recipients"],
            subject=schedule["subject"],
            body=schedule["email_body"] or DEFAULT_EMAIL_BODY,
            attachment_path=result["file_path"],
            interactive=False,     # never open a browser from a job
        )

        email_status = f"Sent to {len(schedule['recipients'])} recipient(s)"
        last_status = "Success"

    except Exception as exc:
        log.exception("Schedule %s: e-mail failed", schedule_id)
        email_status = f"Failed: {exc}"[:300]
        last_status = "Report created, e-mail failed"

    # 6: record the outcome
    store.update_report(report_id, email_status=email_status)
    store.update_schedule(
        schedule_id,
        last_run_at=store.now_iso(),
        last_status=last_status,
    )


# =========================================================
# LIFECYCLE
# =========================================================

def start():
    """Call once, from the process that serves the UI."""
    store.init_db()
    store.mark_interrupted_reports()

    if not scheduler.running:
        scheduler.start()

    for schedule in store.list_schedules(active_only=True):
        register_schedule(schedule)

    log.info("Scheduler started with %d job(s)", len(scheduler.get_jobs()))


def run_now_in_background(schedule_id):
    """Fire a schedule immediately (the 'Run now' button)."""
    scheduler.add_job(
        execute_schedule,
        args=[schedule_id, True],
        id=f"manual:{schedule_id}",
        replace_existing=True,
    )

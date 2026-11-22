"""
report_store.py
===============
Persistent storage (SQLite) for

  * generated reports  - on-demand AND scheduled
  * schedule definitions

Report files live in REPORTS_DIR; the database stores the metadata.
Both survive an application restart as long as DATA_DIR is on a
persistent disk / volume.
"""

import json
import os
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

BASE_DIR = Path(__file__).resolve().parent

DATA_DIR = Path(os.getenv("DATA_DIR", BASE_DIR / "data")).resolve()
REPORTS_DIR = Path(os.getenv("REPORTS_DIR", DATA_DIR / "reports")).resolve()
DB_PATH = DATA_DIR / "app.db"

# Time zone used to DISPLAY timestamps in the UI.
DISPLAY_TZ = os.getenv("DISPLAY_TZ", "Europe/Berlin")

_lock = threading.RLock()


# ---------------------------------------------------------
# helpers
# ---------------------------------------------------------

def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def fmt_ts(value, tz_name=None):
    """UTC ISO string -> 'dd Mon yyyy HH:MM' in the display time zone."""
    if not value:
        return ""

    try:
        dt = datetime.fromisoformat(value)

        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)

        return dt.astimezone(
            ZoneInfo(tz_name or DISPLAY_TZ)
        ).strftime("%d %b %Y %H:%M")

    except Exception:
        return str(value)


def _connect():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def _run(sql, params=(), fetch=None):
    with _lock:
        conn = _connect()

        try:
            cur = conn.execute(sql, params)
            conn.commit()

            if fetch == "one":
                row = cur.fetchone()
                return dict(row) if row else None

            if fetch == "all":
                return [dict(r) for r in cur.fetchall()]

            return None

        finally:
            conn.close()


# ---------------------------------------------------------
# schema
# ---------------------------------------------------------

def init_db():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)

    _run("""
        CREATE TABLE IF NOT EXISTS reports (
            report_id     TEXT PRIMARY KEY,
            name          TEXT,
            report_type   TEXT,      -- On-demand | Scheduled
            status        TEXT,      -- Running | Success | Failed
            report_format TEXT,
            file_path     TEXT,
            user_query    TEXT,
            schedule_id   TEXT,
            record_count  INTEGER,
            error         TEXT,
            email_status  TEXT DEFAULT 'Not sent',
            created_at    TEXT,
            completed_at  TEXT
        )
    """)

    _run("""
        CREATE TABLE IF NOT EXISTS schedules (
            schedule_id   TEXT PRIMARY KEY,
            name          TEXT,
            question      TEXT,
            recipients    TEXT,      -- JSON list
            subject       TEXT,
            email_body    TEXT,
            timezone      TEXT,
            report_format TEXT,
            recurrence    TEXT,      -- Everyday|Weekdays|Weekends|Custom
            slots         TEXT,      -- JSON [{"day":"mon","time":"08:00"}]
            status        TEXT,      -- active | cancelled
            created_at    TEXT,
            updated_at    TEXT,
            last_run_at   TEXT,
            last_status   TEXT
        )
    """)


def mark_interrupted_reports():
    """Reports left in 'Running' by a crash / restart can never finish."""
    _run(
        "UPDATE reports SET status='Failed', "
        "error='Interrupted by application restart', completed_at=? "
        "WHERE status='Running'",
        (now_iso(),),
    )


# ---------------------------------------------------------
# reports
# ---------------------------------------------------------

_REPORT_FIELDS = {
    "name", "report_type", "status", "report_format", "file_path",
    "user_query", "schedule_id", "record_count", "error",
    "email_status", "completed_at",
}


def create_report(name, report_type, report_format, user_query,
                  schedule_id=None):
    report_id = (
        "RPT-"
        + datetime.now().strftime("%Y%m%d-%H%M%S")
        + "-"
        + uuid.uuid4().hex[:4].upper()
    )

    _run(
        "INSERT INTO reports (report_id, name, report_type, status, "
        "report_format, user_query, schedule_id, created_at) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (report_id, name, report_type, "Running", report_format,
         user_query, schedule_id, now_iso()),
    )

    return report_id


def update_report(report_id, **fields):
    bad = set(fields) - _REPORT_FIELDS

    if bad:
        raise ValueError(f"Unknown report fields: {bad}")

    if not fields:
        return

    assignments = ", ".join(f"{k}=?" for k in fields)

    _run(
        f"UPDATE reports SET {assignments} WHERE report_id=?",
        (*fields.values(), report_id),
    )


def get_report(report_id):
    return _run(
        "SELECT * FROM reports WHERE report_id=?",
        (report_id,),
        fetch="one",
    )


def list_reports(search="", report_type="All", status="All"):
    sql = "SELECT * FROM reports WHERE 1=1"
    params = []

    if search and search.strip():
        like = f"%{search.strip().lower()}%"
        sql += (
            " AND (LOWER(report_id) LIKE ? OR LOWER(name) LIKE ?"
            " OR LOWER(user_query) LIKE ?)"
        )
        params += [like, like, like]

    if report_type and report_type != "All":
        sql += " AND report_type=?"
        params.append(report_type)

    if status and status != "All":
        sql += " AND status=?"
        params.append(status)

    sql += " ORDER BY created_at DESC"

    return _run(sql, params, fetch="all")


# ---------------------------------------------------------
# schedules
# ---------------------------------------------------------

_SCHEDULE_FIELDS = {
    "name", "question", "recipients", "subject", "email_body",
    "timezone", "report_format", "recurrence", "slots", "status",
    "last_run_at", "last_status",
}


def _decode_schedule(row):
    if row:
        row["recipients"] = json.loads(row["recipients"] or "[]")
        row["slots"] = json.loads(row["slots"] or "[]")

    return row


def create_schedule(name, question, recipients, subject, email_body,
                    tz, report_format, recurrence, slots):
    schedule_id = "SCH-" + uuid.uuid4().hex[:8].upper()
    ts = now_iso()

    _run(
        "INSERT INTO schedules (schedule_id, name, question, recipients, "
        "subject, email_body, timezone, report_format, recurrence, slots, "
        "status, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (schedule_id, name, question, json.dumps(recipients), subject,
         email_body, tz, report_format, recurrence, json.dumps(slots),
         "active", ts, ts),
    )

    return schedule_id


def update_schedule(schedule_id, **fields):
    bad = set(fields) - _SCHEDULE_FIELDS

    if bad:
        raise ValueError(f"Unknown schedule fields: {bad}")

    for key in ("recipients", "slots"):
        if key in fields:
            fields[key] = json.dumps(fields[key])

    fields["updated_at"] = now_iso()
    assignments = ", ".join(f"{k}=?" for k in fields)

    _run(
        f"UPDATE schedules SET {assignments} WHERE schedule_id=?",
        (*fields.values(), schedule_id),
    )


def get_schedule(schedule_id):
    return _decode_schedule(_run(
        "SELECT * FROM schedules WHERE schedule_id=?",
        (schedule_id,),
        fetch="one",
    ))


def list_schedules(active_only=False):
    sql = "SELECT * FROM schedules"

    if active_only:
        sql += " WHERE status='active'"

    sql += " ORDER BY created_at DESC"

    return [_decode_schedule(r) for r in _run(sql, fetch="all")]
"""
mailbox_service.py
==================
READ-ONLY look into the MRP controller's Gmail mailbox (one mailbox per
plant) to answer, per material:

    * Did the controller send a replenishment request to a vendor?
    * Has the vendor replied?  How long have we been waiting?

This module is completely separate from gmail_service.py (which only
SENDS reports). It uses its own scope (gmail.readonly) and its own
token files, so the existing send flow is not affected.

Configuration file  plant_mailboxes.json  (project root):

{
  "internal_domains": ["mycompany.com"],
  "plants": {
    "1000": {"controller_email": "mrp.1000@mycompany.com",
             "token_file": "token_read_1000.json"},
    "2000": {"controller_email": "mrp.2000@mycompany.com",
             "token_file": "token_read_2000.json"}
  }
}

One-time authorisation per plant (sign in as that controller):

    python mailbox_service.py authorize 1000
"""

import base64
import json
import logging
import os
import re
import sys
import threading
from datetime import datetime, timezone
from email.utils import getaddresses, parseaddr
from pathlib import Path

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

log = logging.getLogger("mailbox")

READ_SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]

BASE_DIR = Path(__file__).resolve().parent
CREDENTIALS_FILE = BASE_DIR / "credentials.json"
CONFIG_FILE = Path(
    os.getenv("PLANT_MAILBOX_CONFIG", BASE_DIR / "plant_mailboxes.json")
)

LOOKBACK_DAYS = int(os.getenv("VENDOR_LOOKBACK_DAYS", "30"))
MATERIALS_PER_QUERY = 12
MAX_MESSAGES_PER_QUERY = 150

_auth_lock = threading.Lock()

# reply_status values
NO_REQUEST = "No request found"
AWAITING = "Awaiting reply"
REPLIED = "Replied"
AUTO_ONLY = "Auto-reply only"
UNAVAILABLE = "Mailbox unavailable"

_AUTO_SUBJECT = re.compile(
    r"(automatic reply|auto[- ]?reply|out of office|abwesenheit|"
    r"automatische antwort|undeliverable|delivery status)",
    re.IGNORECASE,
)


# =========================================================
# CONFIG / AUTH
# =========================================================

def load_config():
    if not CONFIG_FILE.exists():
        raise FileNotFoundError(
            f"{CONFIG_FILE.name} not found. Create it to map each plant "
            "to its MRP controller mailbox (see mailbox_service.py)."
        )

    config = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    config.setdefault("internal_domains", [])
    config.setdefault("plants", {})

    config["internal_domains"] = [
        d.lower().lstrip("@") for d in config["internal_domains"]
    ]

    return config


def _plant_settings(config, plant):
    settings = config["plants"].get(str(plant))

    if not settings:
        raise KeyError(
            f"Plant {plant} has no MRP controller mailbox in "
            f"{CONFIG_FILE.name}."
        )

    return settings


def get_read_service(token_file, interactive=False):
    token_path = BASE_DIR / token_file
    credentials = None

    with _auth_lock:

        if token_path.exists():
            credentials = Credentials.from_authorized_user_file(
                str(token_path), READ_SCOPES
            )

        if credentials and credentials.expired and credentials.refresh_token:
            try:
                credentials.refresh(Request())
            except RefreshError as exc:
                if not interactive:
                    raise RuntimeError(
                        f"Mailbox authorisation ({token_file}) expired. "
                        "Run 'python mailbox_service.py authorize <plant>'."
                    ) from exc
                credentials = None

        if not credentials or not credentials.valid:

            if not interactive:
                raise RuntimeError(
                    f"Mailbox not authorised ({token_file}). Run "
                    "'python mailbox_service.py authorize <plant>' once."
                )

            flow = InstalledAppFlow.from_client_secrets_file(
                str(CREDENTIALS_FILE), READ_SCOPES
            )
            credentials = flow.run_local_server(
                port=0, access_type="offline", prompt="consent"
            )

        token_path.write_text(credentials.to_json())

    return build("gmail", "v1", credentials=credentials,
                 cache_discovery=False)


# =========================================================
# PARSING HELPERS
# =========================================================

def material_pattern(material):
    """
    Match the material number in free text. SAP often pads numeric
    numbers with leading zeros (000000012345) while people write 12345,
    so leading zeros are optional.
    """
    material = str(material).strip()
    stripped = material.lstrip("0") or material

    if stripped.isdigit():
        core = r"0*" + re.escape(stripped)
    else:
        core = re.escape(material)

    return re.compile(
        r"(?<![0-9A-Za-z])" + core + r"(?![0-9A-Za-z])",
        re.IGNORECASE,
    )


def _search_terms(material):
    material = str(material).strip()
    stripped = material.lstrip("0") or material
    return {material, stripped}


def _headers(message):
    return {
        h["name"].lower(): h["value"]
        for h in message.get("payload", {}).get("headers", [])
    }


def _domain(address):
    return address.rsplit("@", 1)[-1].lower() if "@" in address else ""


def _walk_parts(payload):
    yield payload
    for part in payload.get("parts", []) or []:
        yield from _walk_parts(part)


def _message_text(message):
    texts = []

    for part in _walk_parts(message.get("payload", {})):
        if part.get("mimeType") == "text/plain":
            data = part.get("body", {}).get("data")
            if data:
                texts.append(
                    base64.urlsafe_b64decode(data + "==").decode(
                        "utf-8", "replace"
                    )
                )

    return "\n".join(texts) or message.get("snippet", "")


def strip_quoted(text):
    """Keep only what the sender newly wrote (drop the quoted thread)."""
    kept = []

    for line in (text or "").splitlines():
        stripped = line.strip()

        if (
            stripped.startswith(">")
            or re.match(r"^On .+wrote:\s*$", stripped)
            or re.match(r"^-{2,}\s*Original Message", stripped, re.I)
            or re.match(r"^(From|Von):\s", stripped)
        ):
            break

        kept.append(line)

    return re.sub(r"\s+", " ", " ".join(kept)).strip()


def is_auto_reply(message):
    headers = _headers(message)

    auto_submitted = headers.get("auto-submitted", "no").strip().lower()
    precedence = headers.get("precedence", "").strip().lower()

    return (
        auto_submitted not in ("", "no")
        or "x-autoreply" in headers
        or precedence in ("auto_reply", "bulk", "junk")
        or bool(_AUTO_SUBJECT.search(headers.get("subject", "")))
    )


def _iso(ms):
    return datetime.fromtimestamp(
        int(ms) / 1000, tz=timezone.utc
    ).isoformat(timespec="minutes")


# =========================================================
# GMAIL QUERIES
# =========================================================

def _list_ids(service, query, limit=MAX_MESSAGES_PER_QUERY):
    ids, token = [], None

    while len(ids) < limit:
        response = service.users().messages().list(
            userId="me", q=query,
            maxResults=min(100, limit - len(ids)),
            pageToken=token,
        ).execute()

        ids += [m["id"] for m in response.get("messages", [])]
        token = response.get("nextPageToken")

        if not token:
            break

    return ids


def _get_message(service, message_id):
    return service.users().messages().get(
        userId="me", id=message_id, format="full"
    ).execute()


def _find_requests(service, controller, internal_domains, materials):
    """
    Latest replenishment request per material: a message SENT by the
    controller, to at least one external (vendor) recipient, mentioning
    the material number.
    Returns {material: request_dict}
    """

    found = {}
    counts = {}
    patterns = {m: material_pattern(m) for m in materials}

    for i in range(0, len(materials), MATERIALS_PER_QUERY):
        chunk = materials[i:i + MATERIALS_PER_QUERY]

        terms = sorted({t for m in chunk for t in _search_terms(m)})
        query = (
            f"in:sent newer_than:{LOOKBACK_DAYS}d ("
            + " OR ".join(f'"{t}"' for t in terms) + ")"
        )

        for message_id in _list_ids(service, query):
            message = _get_message(service, message_id)
            headers = _headers(message)

            recipients = getaddresses(
                [headers.get("to", ""), headers.get("cc", "")]
            )
            external = sorted({
                addr.lower() for _, addr in recipients
                if addr and _domain(addr) not in internal_domains
            })

            if not external:
                continue                      # internal mail, not a vendor

            sender = parseaddr(headers.get("from", ""))[1].lower()

            if sender and controller and sender != controller.lower():
                continue                      # sent by someone else

            haystack = headers.get("subject", "") + "\n" + \
                _message_text(message)
            sent_ms = int(message["internalDate"])

            for material in chunk:
                if not patterns[material].search(haystack):
                    continue

                counts[material] = counts.get(material, 0) + 1
                current = found.get(material)

                if current is None or sent_ms > current["sent_ms"]:
                    found[material] = {
                        "message_id": message_id,
                        "thread_id": message.get("threadId"),
                        "sent_ms": sent_ms,
                        "subject": headers.get("subject", ""),
                        "vendors": external,
                    }

    for material, request in found.items():
        request["request_count"] = counts.get(material, 1)

    return found


def _first_real_reply(messages, internal_domains, sent_ms):
    """messages -> (real_reply, auto_reply_seen)"""
    real, auto = None, False

    for message in sorted(messages, key=lambda m: int(m["internalDate"])):
        if int(message["internalDate"]) <= sent_ms:
            continue

        sender = parseaddr(_headers(message).get("from", ""))[1].lower()

        if not sender or _domain(sender) in internal_domains:
            continue

        if is_auto_reply(message):
            auto = True
        elif real is None:
            real = message

    return real, auto


def _find_reply(service, request, material, internal_domains):
    thread = service.users().threads().get(
        userId="me", id=request["thread_id"], format="full"
    ).execute()

    real, auto = _first_real_reply(
        thread.get("messages", []), internal_domains, request["sent_ms"]
    )

    if real is None:
        # vendor may have answered in a NEW thread
        sent_seconds = request["sent_ms"] // 1000
        senders = " OR ".join(request["vendors"])
        query = (
            f"in:inbox after:{sent_seconds} from:({senders}) "
            f'"{str(material).lstrip("0") or material}"'
        )

        candidates = [
            _get_message(service, mid)
            for mid in _list_ids(service, query, limit=10)
        ]
        real, auto_2 = _first_real_reply(
            candidates, internal_domains, request["sent_ms"]
        )
        auto = auto or auto_2

    return real, auto


# =========================================================
# PUBLIC API
# =========================================================

def _blank_row(plant, material, controller=""):
    return {
        "material_number": material,
        "plant": plant,
        "controller_email": controller,
        "reply_status": NO_REQUEST,
        "vendor": "",
        "request_sent_at": None,
        "request_subject": "",
        "request_count": 0,
        "hours_waiting": None,
        "reply_at": None,
        "reply_from": "",
        "reply_excerpt": "",
        "error": None,
    }


def check_plant(plant, materials, interactive=False):
    """Return one evidence row per material for one plant mailbox."""
    materials = list(dict.fromkeys(str(m) for m in materials))
    rows = {m: _blank_row(plant, m) for m in materials}

    try:
        config = load_config()
        settings = _plant_settings(config, plant)
        controller = settings["controller_email"]
        internal = config["internal_domains"]

        for row in rows.values():
            row["controller_email"] = controller

        service = get_read_service(
            settings.get("token_file", f"token_read_{plant}.json"),
            interactive=interactive,
        )

        requests = _find_requests(service, controller, internal, materials)
        now = datetime.now(timezone.utc)

        for material, request in requests.items():
            row = rows[material]
            sent_at = datetime.fromtimestamp(
                request["sent_ms"] / 1000, tz=timezone.utc
            )

            row.update(
                vendor=", ".join(request["vendors"]),
                request_sent_at=sent_at.isoformat(timespec="minutes"),
                request_subject=request["subject"],
                request_count=request["request_count"],
            )

            reply, auto_only = _find_reply(
                service, request, material, internal
            )

            if reply is not None:
                headers = _headers(reply)
                row.update(
                    reply_status=REPLIED,
                    reply_at=_iso(reply["internalDate"]),
                    reply_from=parseaddr(headers.get("from", ""))[1],
                    reply_excerpt=strip_quoted(
                        _message_text(reply)
                    )[:600],
                )
            else:
                row["reply_status"] = AUTO_ONLY if auto_only else AWAITING
                row["hours_waiting"] = round(
                    (now - sent_at).total_seconds() / 3600, 1
                )

    except Exception as exc:
        log.exception("Mailbox check failed for plant %s", plant)

        for row in rows.values():
            row["reply_status"] = UNAVAILABLE
            row["error"] = str(exc)[:300]

    return list(rows.values())


def check_vendor_replies(materials_by_plant, interactive=False):
    """
    materials_by_plant: {"1000": ["12345", ...], ...}
    Returns (evidence_rows, warnings)
    """
    rows, warnings = [], []

    for plant, materials in materials_by_plant.items():
        plant_rows = check_plant(plant, materials, interactive)
        rows += plant_rows

        errors = {r["error"] for r in plant_rows if r["error"]}

        for error in errors:
            warnings.append(
                f"Plant {plant}: vendor e-mail check not possible - {error}"
            )

    return rows, warnings


if __name__ == "__main__":
    # python mailbox_service.py authorize 1000
    if len(sys.argv) == 3 and sys.argv[1] == "authorize":
        cfg = load_config()
        s = _plant_settings(cfg, sys.argv[2])
        get_read_service(
            s.get("token_file", f"token_read_{sys.argv[2]}.json"),
            interactive=True,
        )
        print(f"Plant {sys.argv[2]}: mailbox authorised "
        f"({s['controller_email']}).")
    else:
        print("Usage: python mailbox_service.py authorize <plant>")

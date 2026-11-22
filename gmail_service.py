import os
import re
import base64
import mimetypes
import threading

from pathlib import Path
from email.message import EmailMessage

from google.auth.transport.requests import Request
from google.auth.exceptions import RefreshError
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build


SCOPES = [
    "https://www.googleapis.com/auth/gmail.send"
]

BASE_DIR = Path(__file__).resolve().parent

CREDENTIALS_FILE = BASE_DIR / "credentials.json"
TOKEN_FILE = BASE_DIR / "token.json"

_EMAIL_RE = re.compile(r"^[^@\s,;]+@[^@\s,;]+\.[^@\s,;]+$")
_auth_lock = threading.Lock()


def parse_recipients(value):
    """
    Accept 'a@x.com, b@y.com; c@z.com' or a list of addresses.
    Returns a de-duplicated list. Raises ValueError for bad input.
    """

    if isinstance(value, (list, tuple)):
        raw = list(value)
    else:
        raw = re.split(r"[,;\n]+", value or "")

    recipients = []

    for item in raw:
        item = item.strip()

        if not item:
            continue

        if not _EMAIL_RE.match(item):
            raise ValueError(f"Invalid e-mail address: {item}")

        if item.lower() not in [r.lower() for r in recipients]:
            recipients.append(item)

    if not recipients:
        raise ValueError("Enter at least one valid recipient email address.")

    return recipients


def get_gmail_service(interactive=True):
    """
    interactive=True  -> may open a browser for first-time OAuth consent
                         (use from the UI, on a machine with a browser).
    interactive=False -> never opens a browser; raises a clear error if
                         the saved token is missing or no longer valid
                         (use from the background scheduler).
    """

    with _auth_lock:

        credentials = None

        if TOKEN_FILE.exists():
            credentials = Credentials.from_authorized_user_file(
                str(TOKEN_FILE),
                SCOPES,
            )

        if credentials and credentials.expired and credentials.refresh_token:
            try:
                credentials.refresh(Request())

            except RefreshError as exc:
                if not interactive:
                    raise RuntimeError(
                        "Gmail authorisation expired or was revoked. "
                        "Run 'python gmail_service.py' once to sign in "
                        "again."
                    ) from exc

                credentials = None

        if not credentials or not credentials.valid:

            if not interactive:
                raise RuntimeError(
                    "Gmail is not authorised. Run "
                    "'python gmail_service.py' once to sign in."
                )

            if not CREDENTIALS_FILE.exists():
                raise FileNotFoundError(
                    "Gmail credentials.json is missing. "
                    "Download the OAuth Desktop App credentials "
                    "from Google Cloud and place the file in "
                    "the project root."
                )

            flow = InstalledAppFlow.from_client_secrets_file(
                str(CREDENTIALS_FILE),
                SCOPES,
            )

            credentials = flow.run_local_server(
                port=0,
                access_type="offline",
                prompt="consent",
            )

        TOKEN_FILE.write_text(credentials.to_json())

    return build(
        "gmail",
        "v1",
        credentials=credentials,
        cache_discovery=False,
    )


def send_report_email(
    recipient,
    subject: str,
    body: str,
    attachment_path: str,
    interactive: bool = True,
):
    """
    recipient: one address, a comma/semicolon separated string,
               or a list of addresses.
    Returns the Gmail message id.
    """

    recipients = parse_recipients(recipient)

    if not os.path.isfile(attachment_path):
        raise FileNotFoundError(
            f"Report file not found: {attachment_path}"
        )

    service = get_gmail_service(interactive=interactive)

    message = EmailMessage()

    message["To"] = ", ".join(recipients)
    message["Subject"] = subject

    message.set_content(body)

    # Attach the report (PDF, HTML or CSV)
    mime, _ = mimetypes.guess_type(attachment_path)
    maintype, subtype = (mime or "application/octet-stream").split("/", 1)

    with open(attachment_path, "rb") as attachment:
        file_data = attachment.read()

    message.add_attachment(
        file_data,
        maintype=maintype,
        subtype=subtype,
        filename=os.path.basename(attachment_path),
    )

    encoded_message = base64.urlsafe_b64encode(
        message.as_bytes()
    ).decode()

    result = (
        service.users()
        .messages()
        .send(
            userId="me",
            body={"raw": encoded_message},
        )
        .execute()
    )

    return result["id"]


if __name__ == "__main__":
    # One-time authorisation:  python gmail_service.py
    get_gmail_service(interactive=True)
    print("Gmail authorised. token.json saved.")
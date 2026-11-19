
import os
import base64

from pathlib import Path
from email.message import EmailMessage

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build


SCOPES = [
    "https://www.googleapis.com/auth/gmail.send"
]

BASE_DIR = Path(__file__).resolve().parent

CREDENTIALS_FILE = BASE_DIR / "credentials.json"
TOKEN_FILE = BASE_DIR / "token.json"


def get_gmail_service():

    if not CREDENTIALS_FILE.exists():
        raise FileNotFoundError(
            "Gmail credentials.json is missing. "
            "Download the OAuth Desktop App credentials "
            "from Google Cloud and place the file in "
            "the project root."
        )

    credentials = None

    if TOKEN_FILE.exists():
        credentials = Credentials.from_authorized_user_file(
            str(TOKEN_FILE),
            SCOPES,
        )

    if credentials and credentials.expired and credentials.refresh_token:
        credentials.refresh(Request())

    elif not credentials or not credentials.valid:

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
    )



def send_report_email(
    recipient: str,
    subject: str,
    body: str,
    attachment_path: str,
):

    if not recipient or "@" not in recipient:
        raise ValueError("Enter a valid recipient email address.")

    if not os.path.isfile(attachment_path):
        raise FileNotFoundError(
            f"PDF report not found: {attachment_path}"
        )

    service = get_gmail_service()

    message = EmailMessage()

    message["To"] = recipient
    message["Subject"] = subject

    message.set_content(body)

    # Attach PDF report
    with open(attachment_path, "rb") as attachment:
        pdf_data = attachment.read()

    message.add_attachment(
        pdf_data,
        maintype="application",
        subtype="pdf",
        filename=os.path.basename(attachment_path),
    )

    # Encode email
    encoded_message = base64.urlsafe_b64encode(
        message.as_bytes()
    ).decode()

    # Send email through Gmail API
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


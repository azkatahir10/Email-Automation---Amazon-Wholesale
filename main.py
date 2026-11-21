
import csv
import re
import base64
import argparse

from pathlib import Path
from datetime import datetime, timezone
from email.message import EmailMessage
from urllib.parse import urlparse

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build


# --------------------------------------------------
# CONFIGURATION
# --------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent

CSV_FILE = BASE_DIR / "brands.csv"
TEMPLATE_FILE = BASE_DIR / "email_template.txt"
CREDENTIALS_FILE = BASE_DIR / "credentials.json"
TOKEN_FILE = BASE_DIR / "token.json"
LOG_FILE = BASE_DIR / "processed_leads.csv"
CONTACT_FORM_FILE = BASE_DIR / "contact_form_messages.txt"

SCOPES = ["https://www.googleapis.com/auth/gmail.compose"]


# --------------------------------------------------
# 1. READ EMAIL TEMPLATE
# --------------------------------------------------

def load_template():

    if not TEMPLATE_FILE.exists():
        raise FileNotFoundError("email_template.txt not found.")

    with open(TEMPLATE_FILE, "r", encoding="utf-8") as file:
        content = file.read().strip()

    lines = content.splitlines()

    if not lines or not lines[0].startswith("Subject:"):
        raise ValueError(
            "The first line of email_template.txt "
            "must start with 'Subject:'"
        )

    subject_template = lines[0].replace(
        "Subject:", "", 1
    ).strip()

    body_template = "\n".join(lines[1:]).strip()

    if not subject_template or not body_template:
        raise ValueError("Email subject or body is empty.")

    return subject_template, body_template


# --------------------------------------------------
# 2. READ CSV FILE
# --------------------------------------------------

def read_csv():

    if not CSV_FILE.exists():
        raise FileNotFoundError("brands.csv not found.")

    with open(CSV_FILE, "r", encoding="utf-8-sig", newline="") as file:

        sample = file.read(4096)
        file.seek(0)

        try:
            dialect = csv.Sniffer().sniff(
                sample,
                delimiters=",;\t"
            )
        except csv.Error:
            dialect = csv.excel

        reader = csv.DictReader(file, dialect=dialect)

        if not reader.fieldnames:
            raise ValueError("brands.csv has no header row.")

        def normalize_header(header):
            return header.replace("\ufeff", "").strip().lower()

        header_map = {
            normalize_header(header): header
            for header in reader.fieldnames
            if header is not None
        }

        required_columns = {
            "amazon url": "Amazon URL",
            "asin": "ASIN",
            "product description": "Product Description",
            "contact email": "Contact Email",
            "brand name": "Brand name"
        }

        missing_columns = [
            display_name
            for normalized, display_name in required_columns.items()
            if normalized not in header_map
        ]

        if missing_columns:
            raise ValueError(
                f"Missing CSV columns: {missing_columns}\n"
                "Expected: Amazon URL, ASIN, Product Description, "
                "Contact Email, Brand name"
            )

        leads = []

        for row in reader:

            lead = {
                "amazon_url": (
                    row.get(header_map["amazon url"]) or ""
                ).strip(),

                "asin": (
                    row.get(header_map["asin"]) or ""
                ).strip(),

                "product_description": (
                    row.get(header_map["product description"]) or ""
                ).strip(),

                "email": (
                    row.get(header_map["contact email"]) or ""
                ).strip(),

                "brand_name": (
                    row.get(header_map["brand name"]) or ""
                ).strip()
            }

            if not any(lead.values()):
                continue

            leads.append(lead)

    return leads


# --------------------------------------------------
# 3. VALIDATE AMAZON URL
# --------------------------------------------------

def valid_amazon_url(url):

    try:
        parsed = urlparse(url)

        return (
            parsed.scheme in ("http", "https")
            and bool(parsed.netloc)
        )

    except Exception:
        return False


# --------------------------------------------------
# 4. IDENTIFY EMAIL OR CONTACT FORM
# --------------------------------------------------

def identify_contact_type(contact):

    email_pattern = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"

    if re.match(email_pattern, contact):
        return "Email"

    try:
        parsed = urlparse(contact)

        if (
            parsed.scheme in ("http", "https")
            and bool(parsed.netloc)
        ):
            return "Contact Form"

    except Exception:
        pass

    return "Invalid"


# --------------------------------------------------
# 5. VALIDATE LEADS
# --------------------------------------------------

def validate_lead(lead):

    required_values = [
        lead["amazon_url"],
        lead["asin"],
        lead["product_description"],
        lead["email"],
        lead["brand_name"]
    ]

    if not all(required_values):
        return False, "Missing required information"

    if not valid_amazon_url(lead["amazon_url"]):
        return False, "Invalid Amazon URL"

    contact_type = identify_contact_type(lead["email"])

    if contact_type == "Invalid":
        return False, "Invalid email address or contact form URL"

    return True, contact_type


# --------------------------------------------------
# 6. PERSONALIZE EMAIL
# --------------------------------------------------

def generate_email(lead, subject_template, body_template):

    template_values = {
        "brand_name": lead["brand_name"],
        "product_description": lead["product_description"],
        "amazon_url": lead["amazon_url"],
        "asin": lead["asin"]
    }

    subject = subject_template.format(**template_values)

    body = body_template.format(**template_values)

    return subject, body


# --------------------------------------------------
# 7. GENERATE CONTACT FORM MESSAGES
# --------------------------------------------------

def generate_contact_form_messages(
    contact_form_leads,
    subject_template,
    body_template
):

    with open(
        CONTACT_FORM_FILE,
        "w",
        encoding="utf-8"
    ) as file:

        file.write("=" * 70 + "\n")
        file.write("       WHOLESALE CONTACT FORM MESSAGES\n")
        file.write("=" * 70 + "\n\n")

        if not contact_form_leads:
            file.write("No contact form leads found.\n")

        for index, lead in enumerate(
            contact_form_leads,
            start=1
        ):

            # Use exactly the same template as Gmail drafts.
            subject, body = generate_email(
                lead,
                subject_template,
                body_template
            )

            file.write("=" * 70 + "\n")
            file.write(f"CONTACT FORM LEAD #{index}\n")
            file.write("=" * 70 + "\n\n")

            # Internal reference information only.
            file.write("LEAD INFORMATION (FOR REFERENCE ONLY)\n")
            file.write("-" * 70 + "\n")

            file.write(f"Brand: {lead['brand_name']}\n")
            file.write(
                f"Product: {lead['product_description']}\n"
            )
            file.write(f"ASIN: {lead['asin']}\n")
            file.write(f"Amazon URL: {lead['amazon_url']}\n")
            file.write(
                f"Contact Form URL: {lead['email']}\n\n"
            )

            # Actual message to copy.
            # No Amazon URL, ASIN or contact form URL is added here.
            file.write("SUBJECT:\n")
            file.write(subject + "\n\n")

            file.write("MESSAGE TO COPY INTO CONTACT FORM:\n")
            file.write("-" * 70 + "\n")
            file.write(body.strip() + "\n\n\n")

    print(
        f"Contact form messages saved to: "
        f"{CONTACT_FORM_FILE.name}"
    )


# --------------------------------------------------
# 8. CHECK PREVIOUSLY PROCESSED LEADS
# --------------------------------------------------

def load_processed_leads():

    processed = set()

    if not LOG_FILE.exists():
        return processed

    with open(
        LOG_FILE,
        "r",
        encoding="utf-8",
        newline=""
    ) as file:

        reader = csv.DictReader(file)

        for row in reader:

            if row.get("Status") == "CREATED":

                key = (
                    row.get("Contact Email", "").lower(),
                    row.get("ASIN", "").upper()
                )

                processed.add(key)

    return processed


# --------------------------------------------------
# 9. SAVE PROCESSING RESULTS
# --------------------------------------------------

def save_result(lead, status, draft_id="", error=""):

    file_exists = LOG_FILE.exists()

    with open(
        LOG_FILE,
        "a",
        encoding="utf-8",
        newline=""
    ) as file:

        writer = csv.writer(file)

        if not file_exists:

            writer.writerow([
                "Timestamp",
                "Brand name",
                "Contact Email",
                "ASIN",
                "Status",
                "Draft ID",
                "Error"
            ])

        writer.writerow([
            datetime.now(timezone.utc).isoformat(),
            lead["brand_name"],
            lead["email"],
            lead["asin"],
            status,
            draft_id,
            error
        ])


# --------------------------------------------------
# 10. GMAIL AUTHENTICATION
# --------------------------------------------------

def authenticate_gmail():

    if not CREDENTIALS_FILE.exists():

        raise FileNotFoundError(
            "credentials.json not found. "
            "Download your OAuth credentials from Google Cloud."
        )

    credentials = None

    if TOKEN_FILE.exists():

        credentials = Credentials.from_authorized_user_file(
            str(TOKEN_FILE),
            SCOPES
        )

    if (
        credentials
        and credentials.expired
        and credentials.refresh_token
    ):

        credentials.refresh(Request())

    if not credentials or not credentials.valid:

        flow = InstalledAppFlow.from_client_secrets_file(
            str(CREDENTIALS_FILE),
            SCOPES
        )

        credentials = flow.run_local_server(port=0)

    with open(
        TOKEN_FILE,
        "w",
        encoding="utf-8"
    ) as token:

        token.write(credentials.to_json())

    service = build(
        "gmail",
        "v1",
        credentials=credentials,
        cache_discovery=False
    )

    return service


# --------------------------------------------------
# 11. CREATE GMAIL DRAFT
# --------------------------------------------------

def create_gmail_draft(service, recipient, subject, body):

    message = EmailMessage()

    message["To"] = recipient
    message["Subject"] = subject

    message.set_content(body)

    encoded_message = base64.urlsafe_b64encode(
        message.as_bytes()
    ).decode()

    draft = {
        "message": {
            "raw": encoded_message
        }
    }

    result = service.users().drafts().create(
        userId="me",
        body=draft
    ).execute()

    return result["id"]


# --------------------------------------------------
# 12. MAIN APPLICATION
# --------------------------------------------------

def main():

    parser = argparse.ArgumentParser(
        description="Create personalized Gmail drafts from CSV."
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview emails without creating Gmail drafts."
    )

    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Limit email and contact-form leads separately. 0 means all."
    )

    args = parser.parse_args()

    if args.limit < 0:
        raise ValueError("--limit cannot be negative.")

    print("\n====================================")
    print("     GMAIL DRAFT AUTOMATION")
    print("====================================\n")

    # Load template and CSV.

    subject_template, body_template = load_template()

    leads = read_csv()

    print(f"Total CSV rows: {len(leads)}")

    # Load previously created Gmail drafts.

    processed = load_processed_leads()

    pending_email_leads = []
    contact_form_leads = []

    invalid_count = 0
    duplicate_count = 0

    # Classify and validate leads.

    for lead in leads:

        valid, reason = validate_lead(lead)

        if not valid:

            invalid_count += 1

            print(
                f"[INVALID] {lead['brand_name']}: {reason}"
            )

            continue

        if reason == "Contact Form":

            contact_form_leads.append(lead)

            print(
                f"[CONTACT FORM] {lead['brand_name']}: "
                "Message will be generated."
            )

            continue

        # Only email leads are checked against Gmail history.

        key = (
            lead["email"].lower(),
            lead["asin"].upper()
        )

        if key in processed:

            duplicate_count += 1

            print(
                f"[DUPLICATE] {lead['brand_name']}: "
                "Gmail draft already created."
            )

            continue

        pending_email_leads.append(lead)

    # Apply limit independently to each category.

    if args.limit > 0:

        pending_email_leads = pending_email_leads[:args.limit]

        contact_form_leads = contact_form_leads[:args.limit]

    # Generate contact-form document before Gmail confirmation.
    # This happens even if there are no new email leads.

    generate_contact_form_messages(
        contact_form_leads,
        subject_template,
        body_template
    )

    # Summary.

    print("\n------------------------------------")
    print(
        f"Valid pending email leads: "
        f"{len(pending_email_leads)}"
    )
    print(
        f"Contact form leads:        "
        f"{len(contact_form_leads)}"
    )
    print(f"Invalid leads:             {invalid_count}")
    print(f"Already processed:         {duplicate_count}")
    print("------------------------------------\n")

    # Preview email drafts.

    for index, lead in enumerate(
        pending_email_leads[:3],
        start=1
    ):

        subject, body = generate_email(
            lead,
            subject_template,
            body_template
        )

        print(f"PREVIEW {index}")
        print(f"To: {lead['email']}")
        print(f"Subject: {subject}")
        print("\n" + body)
        print("\n------------------------------------\n")

    # Dry run: no Gmail drafts are created.

    if args.dry_run:

        print("DRY RUN COMPLETE.")
        print("No Gmail drafts were created.")
        print(
            f"{len(contact_form_leads)} contact-form "
            "messages were generated."
        )

        return

    # If there are no email leads, finish here.

    if not pending_email_leads:

        print("No new Gmail drafts to create.")

        print(
            f"Contact-form messages generated: "
            f"{len(contact_form_leads)}"
        )

        return

    # Confirm before creating Gmail drafts.

    confirmation = input(
        f"Create {len(pending_email_leads)} Gmail drafts? (yes/no): "
    ).strip().lower()

    if confirmation != "yes":

        print("Gmail draft creation cancelled.")
        print(
            "Your contact_form_messages.txt file "
            "has still been generated."
        )

        return

    # Authenticate Gmail.

    print("\nConnecting to Gmail...")

    service = authenticate_gmail()

    print("Gmail authentication successful.\n")

    # Create Gmail drafts.

    success_count = 0
    failure_count = 0

    for index, lead in enumerate(
        pending_email_leads,
        start=1
    ):

        print(
            f"[{index}/{len(pending_email_leads)}] "
            f"Processing {lead['brand_name']}..."
        )

        try:

            subject, body = generate_email(
                lead,
                subject_template,
                body_template
            )

            draft_id = create_gmail_draft(
                service,
                lead["email"],
                subject,
                body
            )

            save_result(
                lead,
                "CREATED",
                draft_id=draft_id
            )

            success_count += 1

            processed.add((
                lead["email"].lower(),
                lead["asin"].upper()
            ))

            print(
                f"SUCCESS: Draft created. ID: {draft_id}"
            )

        except Exception as error:

            save_result(
                lead,
                "FAILED",
                error=str(error)
            )

            failure_count += 1

            print(f"FAILED: {error}")

    # Final report.

    print("\n====================================")
    print("          FINAL REPORT")
    print("====================================")

    print(f"Drafts created:        {success_count}")
    print(f"Failed:                {failure_count}")
    print(f"Invalid:               {invalid_count}")
    print(f"Duplicates:            {duplicate_count}")
    print(
        f"Contact form messages: "
        f"{len(contact_form_leads)}"
    )

    print("\nCheck your Gmail Drafts folder.")
    print("See processed_leads.csv for details.")
    print("See contact_form_messages.txt for contact-form messages.")


# --------------------------------------------------
# ENTRY POINT
# --------------------------------------------------

if __name__ == "__main__":

    try:
        main()

    except Exception as error:

        print(f"\nAPPLICATION ERROR: {error}")

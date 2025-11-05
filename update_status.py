# -*- coding: utf-8 -*-
"""Update job application status based on Gmail "지원 완료" emails.

This script looks up Gmail messages that indicate an application has been
submitted and persists the result into ``docs/application_status.json`` so that
other parts of the pipeline (crawler / pages) can reference the latest status
information.

The script is intentionally defensive:
- Credentials are read either from the ``GMAIL_TOKEN_JSON`` environment variable
  (useful for GitHub Actions) or from a local ``token.json`` file for manual
  runs.
- When credentials are missing the script exits gracefully so that the CI job
  does not fail.
- Message payload parsing works with multipart and plain text payloads and falls
  back to the subject if the email body does not contain structured fields.
"""

from __future__ import annotations

import base64
import json
import os
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
STATUS_PATH = Path("docs/application_status.json")
GMAIL_QUERY = "subject:(지원 완료 OR 입사지원 OR 접수완료 OR 지원이 완료되었습니다)"
MAX_MESSAGES = 200


def normalize_text(value: Optional[str]) -> str:
    """Normalize text for fuzzy matching.

    Lowercase the text, strip leading/trailing whitespace and collapse multiple
    spaces into one so that company/job title comparisons become more lenient.
    """

    if not value:
        return ""
    collapsed = re.sub(r"\s+", " ", value.strip())
    return collapsed.lower()


def load_credentials() -> Optional[Credentials]:
    """Load Gmail credentials from env (preferred) or a local token file."""

    token_env = os.environ.get("GMAIL_TOKEN_JSON")
    if token_env:
        try:
            info = json.loads(token_env)
            return Credentials.from_authorized_user_info(info, scopes=SCOPES)
        except json.JSONDecodeError as exc:  # pragma: no cover - defensive
            raise RuntimeError("GMAIL_TOKEN_JSON 환경변수를 JSON으로 파싱할 수 없습니다.") from exc

    token_file = Path("token.json")
    if token_file.exists():
        return Credentials.from_authorized_user_file(str(token_file), scopes=SCOPES)
    return None


def decode_message_data(data: Optional[str]) -> str:
    """Decode a base64url encoded Gmail body fragment."""

    if not data:
        return ""
    padding = (-len(data)) % 4
    if padding:
        data += "=" * padding
    try:
        return base64.urlsafe_b64decode(data.encode("utf-8")).decode("utf-8", errors="ignore")
    except (ValueError, UnicodeDecodeError):  # pragma: no cover - defensive
        return ""


def extract_body(payload: Optional[Dict]) -> str:
    """Extract the first text body from a Gmail message payload."""

    if not payload:
        return ""

    parts = payload.get("parts", []) or []
    for part in parts:
        mime = part.get("mimeType", "")
        if mime.startswith("text/plain"):
            body_data = part.get("body", {}).get("data")
            text = decode_message_data(body_data)
            if text:
                return text

    for part in parts:
        nested = extract_body(part)
        if nested:
            return nested

    body_data = payload.get("body", {}).get("data")
    return decode_message_data(body_data)


def get_header(headers: Iterable[Dict[str, str]], name: str) -> str:
    """Return a header value case-insensitively."""

    for header in headers:
        if header.get("name", "").lower() == name.lower():
            return header.get("value", "")
    return ""


def parse_company_title(subject: str, body: str) -> Tuple[str, str]:
    """Attempt to extract company/title values from the email body/subject."""

    company = ""
    title = ""

    for line in (ln.strip() for ln in body.splitlines() if ln.strip()):
        if not company:
            match = re.search(r"(?:회사|기업)명\s*[:：]\s*(.+)", line)
            if match:
                company = match.group(1).strip()
                continue
        if not title:
            match = re.search(r"(?:공고|지원|채용)\s*명\s*[:：]\s*(.+)", line)
            if match:
                title = match.group(1).strip()
                continue
        if not title:
            match = re.search(r"(?:지원(?:분야|직무))\s*[:：]\s*(.+)", line)
            if match:
                title = match.group(1).strip()

    if not company or not title:
        subject_patterns = [
            re.compile(r"\[(?P<company>.+?)\]\s*(?P<title>.+?)\s*(?:지원\s*완료|입사지원)", re.IGNORECASE),
            re.compile(r"(?P<company>.+?)\s*-\s*(?P<title>.+?)\s*(?:지원\s*완료|입사지원)", re.IGNORECASE),
        ]
        for pattern in subject_patterns:
            match = pattern.search(subject)
            if match:
                if not company:
                    company = match.group("company").strip()
                if not title:
                    title = match.group("title").strip()
                break

    if not title and subject:
        title = subject.strip()

    return company, title


def parse_email_datetime(date_value: str) -> Optional[str]:
    """Convert a raw Date header into an ISO 8601 UTC string."""

    if not date_value:
        return None
    try:
        dt = parsedate_to_datetime(date_value)
    except (TypeError, ValueError):  # pragma: no cover - defensive
        return None
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


def load_status_data() -> Dict[str, object]:
    """Load the persisted status data structure."""

    if STATUS_PATH.exists():
        try:
            data = json.loads(STATUS_PATH.read_text(encoding="utf-8"))
        except json.JSONDecodeError:  # pragma: no cover - defensive
            data = {}
        if isinstance(data, dict):
            data.setdefault("messages", [])
            return data
    return {"messages": []}


def save_status_data(data: Dict[str, object]) -> None:
    STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATUS_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def fetch_new_messages(service, known_ids: set[str]) -> List[Dict[str, object]]:
    """Fetch new Gmail messages that are not yet present in ``known_ids``."""

    fetched_ids: List[str] = []
    next_token: Optional[str] = None

    while True:
        try:
            response = service.users().messages().list(
                userId="me",
                q=GMAIL_QUERY,
                pageToken=next_token,
                maxResults=100,
            ).execute()
        except HttpError as exc:  # pragma: no cover - network safety
            print(f"❌ Gmail API 오류로 상태 업데이트를 중단합니다: {exc}")
            return []

        messages = response.get("messages", [])
        for message in messages:
            msg_id = message.get("id")
            if not msg_id or msg_id in known_ids:
                continue
            fetched_ids.append(msg_id)
            if len(fetched_ids) >= MAX_MESSAGES:
                break

        next_token = response.get("nextPageToken")
        if not next_token or len(fetched_ids) >= MAX_MESSAGES:
            break

    new_entries: List[Dict[str, object]] = []
    for msg_id in fetched_ids:
        try:
            message = service.users().messages().get(userId="me", id=msg_id, format="full").execute()
        except HttpError as exc:  # pragma: no cover - network safety
            print(f"⚠️ 메시지 {msg_id} 상세 조회에 실패했습니다: {exc}")
            continue

        payload = message.get("payload", {})
        headers = payload.get("headers", [])
        subject = get_header(headers, "Subject")
        body_text = extract_body(payload)
        company, title = parse_company_title(subject, body_text)
        email_timestamp = parse_email_datetime(get_header(headers, "Date"))

        entry = {
            "message_id": msg_id,
            "thread_id": message.get("threadId"),
            "subject": subject,
            "company": company,
            "title": title,
            "status": "지원 완료",
            "email_date": get_header(headers, "Date"),
            "email_timestamp": email_timestamp,
            "synced_at": datetime.now(timezone.utc).isoformat(),
            "snippet": message.get("snippet", ""),
            "normalized_company": normalize_text(company),
            "normalized_title": normalize_text(title),
        }
        new_entries.append(entry)

    return new_entries


def merge_status_messages(existing: List[Dict[str, object]], new_entries: List[Dict[str, object]]) -> List[Dict[str, object]]:
    """Merge new message entries into the existing list (by message id)."""

    merged: Dict[str, Dict[str, object]] = {}
    for item in existing + new_entries:
        msg_id = str(item.get("message_id"))
        merged[msg_id] = item

    result = list(merged.values())
    result.sort(key=lambda item: item.get("email_timestamp") or item.get("synced_at") or "", reverse=True)
    return result


def main() -> None:
    creds = load_credentials()
    if not creds:
        print("⚠️ Gmail 인증 정보를 찾을 수 없어 지원 상태 업데이트를 건너뜁니다.")
        return

    service = build("gmail", "v1", credentials=creds, cache_discovery=False)

    data = load_status_data()
    known_ids = {str(msg.get("message_id")) for msg in data.get("messages", []) if msg.get("message_id")}

    new_entries = fetch_new_messages(service, known_ids)
    if not new_entries:
        print("ℹ️ 새로운 지원 완료 메일이 없어 상태 업데이트를 생략합니다.")
        return

    data["messages"] = merge_status_messages(data.get("messages", []), new_entries)
    data["last_synced_at"] = datetime.now(timezone.utc).isoformat()
    save_status_data(data)

    print(f"✅ {len(new_entries)}건의 지원 완료 메일을 상태 파일에 반영했습니다.")


if __name__ == "__main__":  # pragma: no cover - script entry point
    main()

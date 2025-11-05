# -*- coding: utf-8 -*-
"""Update application statuses in the Saramin HTML using Gmail confirmation emails."""
from __future__ import annotations

import base64
import json
import os
import re
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import List, Optional, Sequence, Set, Tuple

from bs4 import BeautifulSoup
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
STATUS_LABEL = os.getenv("STATUS_COLUMN_TITLE", "지원상태")
HTML_PATH = Path(os.getenv("HTML_PATH", "docs/saramin_results_latest.html"))
STATUS_JSON_PATH = Path(os.getenv("STATUS_JSON_PATH", "docs/application_status.json"))
GMAIL_QUERY = os.getenv(
    "GMAIL_QUERY",
    "subject:(지원 완료 OR 입사지원 완료 OR 지원이 완료되었습니다)"
)
GMAIL_MAX_RESULTS = int(os.getenv("GMAIL_MAX_RESULTS", "100"))


@dataclass
class AppliedJob:
    """Light-weight container for a single application record."""

    message_id: str
    subject: str
    received_at: Optional[str]
    company: Optional[str]
    title: Optional[str]
    rec_idx: Optional[str]

    def normalized_pair(self) -> Tuple[str, str]:
        return normalize_text(self.company), normalize_text(self.title)


class GmailTokenError(RuntimeError):
    """Raised when Gmail credentials are missing or malformed."""


def normalize_text(value: Optional[str]) -> str:
    if not value:
        return ""
    return re.sub(r"\s+", "", value).lower()


def decode_base64(data: str) -> bytes:
    padding = len(data) % 4
    if padding:
        data += "=" * (4 - padding)
    return base64.urlsafe_b64decode(data.encode("utf-8"))


def load_credentials() -> Credentials:
    token_json = os.getenv("GMAIL_TOKEN_JSON")
    token_path = Path(os.getenv("GMAIL_TOKEN_PATH", "token.json"))

    data: Optional[dict] = None
    if token_json:
        try:
            data = json.loads(token_json)
        except json.JSONDecodeError:
            try:
                decoded = decode_base64(token_json).decode("utf-8")
                data = json.loads(decoded)
            except Exception as exc:  # pragma: no cover - defensive guard
                raise GmailTokenError("환경 변수 GMAIL_TOKEN_JSON을 JSON 또는 base64(JSON) 형태로 설정하세요.") from exc
    elif token_path.exists():
        data = json.loads(token_path.read_text(encoding="utf-8"))

    if not data:
        raise GmailTokenError("Gmail API 토큰 정보를 찾을 수 없습니다. token.json 파일 또는 GMAIL_TOKEN_JSON 환경 변수를 제공하세요.")

    return Credentials.from_authorized_user_info(data, SCOPES)


def build_gmail_service() -> "Resource":
    creds = load_credentials()
    return build("gmail", "v1", credentials=creds, cache_discovery=False)


def extract_payload_text(payload: dict) -> str:
    text_chunks: List[str] = []

    data = payload.get("body", {}).get("data")
    mime_type = payload.get("mimeType", "")
    if data:
        try:
            decoded = decode_base64(data)
            if "html" in mime_type:
                text_chunks.append(BeautifulSoup(decoded, "html.parser").get_text("\n"))
            else:
                text_chunks.append(decoded.decode("utf-8", errors="ignore"))
        except Exception:  # pragma: no cover - best effort decoding
            text_chunks.append("")

    for part in payload.get("parts", []) or []:
        text_chunks.append(extract_payload_text(part))

    return "\n".join(chunk for chunk in text_chunks if chunk)


def find_header(headers: Sequence[dict], name: str) -> Optional[str]:
    for header in headers:
        if header.get("name") == name:
            return header.get("value")
    return None


def parse_company_and_title(text: str, subject: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    company = None
    title = None
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if not company:
            m = re.search(r"(회사명|기업명)\s*[:：]\s*(.+)", line)
            if m:
                company = m.group(2).strip()
                continue
        if not title:
            m = re.search(r"(공고명|채용공고|지원포지션|모집부문)\s*[:：]\s*(.+)", line)
            if m:
                title = m.group(2).strip()
    if subject:
        quoted = re.findall(r"[\[『"'“](.+?)[\]』"'”]", subject)
        if quoted:
            if not company:
                company = quoted[0].strip()
            if len(quoted) > 1 and not title:
                title = quoted[1].strip()
    return company, title


def parse_rec_idx(*texts: Optional[str]) -> Optional[str]:
    for text in texts:
        if not text:
            continue
        m = re.search(r"rec_idx=(\d+)", text)
        if m:
            return m.group(1)
    return None


def parse_received_at(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    try:
        dt = parsedate_to_datetime(value)
        if dt:
            return dt.isoformat()
    except Exception:  # pragma: no cover - guard against format surprises
        return None
    return None


def fetch_applied_jobs(service) -> List[AppliedJob]:
    jobs: List[AppliedJob] = []
    request = service.users().messages().list(
        userId="me",
        q=GMAIL_QUERY,
        maxResults=min(GMAIL_MAX_RESULTS, 500),
    )

    while request is not None:
        response = request.execute()
        for meta in response.get("messages", []):
            msg = service.users().messages().get(userId="me", id=meta["id"], format="full").execute()
            payload = msg.get("payload", {})
            headers = payload.get("headers", [])
            subject = find_header(headers, "Subject") or ""
            body_text = extract_payload_text(payload)
            company, title = parse_company_and_title(body_text, subject)
            rec_idx = parse_rec_idx(body_text, subject)
            received_at = parse_received_at(find_header(headers, "Date"))
            jobs.append(
                AppliedJob(
                    message_id=meta["id"],
                    subject=subject,
                    received_at=received_at,
                    company=company,
                    title=title,
                    rec_idx=rec_idx,
                )
            )
            if len(jobs) >= GMAIL_MAX_RESULTS:
                break
        if len(jobs) >= GMAIL_MAX_RESULTS:
            break
        request = service.users().messages().list_next(request, response)
    return jobs


def determine_status(
    rec_idx: Optional[str],
    company: str,
    title: str,
    applied_rec_ids: Set[str],
    applied_pairs: Set[Tuple[str, str]],
    applied_titles: Set[str],
    applied_companies: Set[str],
) -> str:
    company_norm = normalize_text(company)
    title_norm = normalize_text(title)
    if rec_idx and rec_idx in applied_rec_ids:
        return "지원완료"
    if (company_norm, title_norm) in applied_pairs and company_norm and title_norm:
        return "지원완료"
    if title_norm and title_norm in applied_titles:
        return "지원완료(제목추정)"
    if company_norm and company_norm in applied_companies:
        return "지원완료(회사추정)"
    return "미지원"


def update_html(applied_jobs: Sequence[AppliedJob]) -> List[dict]:
    if not HTML_PATH.exists():
        raise FileNotFoundError(f"HTML 파일을 찾을 수 없습니다: {HTML_PATH}")

    html_text = HTML_PATH.read_text(encoding="utf-8")
    soup = BeautifulSoup(html_text, "html.parser")
    table = soup.find("table")
    if table is None:
        raise RuntimeError("HTML 테이블을 찾을 수 없습니다.")

    header_row = table.find("tr")
    headers = [th.get_text(strip=True) for th in header_row.find_all("th")]
    if STATUS_LABEL not in headers:
        new_th = soup.new_tag("th")
        new_th.string = STATUS_LABEL
        header_row.append(new_th)
        headers.append(STATUS_LABEL)
    status_idx = headers.index(STATUS_LABEL)
    title_idx = headers.index("제목") if "제목" in headers else 0
    company_idx = headers.index("회사") if "회사" in headers else 1

    applied_rec_ids = {job.rec_idx for job in applied_jobs if job.rec_idx}
    applied_pairs = {job.normalized_pair() for job in applied_jobs if job.company or job.title}
    applied_titles = {normalize_text(job.title) for job in applied_jobs if job.title}
    applied_companies = {normalize_text(job.company) for job in applied_jobs if job.company}

    records: List[dict] = []
    tbody = table.find("tbody")
    if tbody is None:
        return records

    for row in tbody.find_all("tr"):
        cells = row.find_all("td")
        link = row.find("a", href=True)
        href = link["href"] if link else None
        rec_idx = parse_rec_idx(href)
        title_text = cells[title_idx].get_text(strip=True) if len(cells) > title_idx else ""
        company_text = cells[company_idx].get_text(strip=True) if len(cells) > company_idx else ""
        status_text = determine_status(
            rec_idx,
            company_text,
            title_text,
            applied_rec_ids,
            applied_pairs,
            applied_titles,
            applied_companies,
        )
        if status_idx < len(cells):
            cells[status_idx].string = status_text
        else:
            new_td = soup.new_tag("td")
            new_td.string = status_text
            row.append(new_td)

        records.append(
            {
                "title": title_text,
                "company": company_text,
                "status": status_text,
                "rec_idx": rec_idx,
                "link": href,
            }
        )

    fragments: List[str] = []
    heading = soup.find("h2")
    if heading:
        fragments.append(str(heading))
    fragments.append(str(table))
    HTML_PATH.write_text("\n".join(fragments), encoding="utf-8")
    return records


def save_status_json(records: Sequence[dict]) -> None:
    STATUS_JSON_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATUS_JSON_PATH.write_text(json.dumps(list(records), ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> None:
    service = build_gmail_service()
    applied_jobs = fetch_applied_jobs(service)
    if not applied_jobs:
        print("⚠️ 지원 완료 메일을 찾지 못했습니다. HTML은 수정하지 않습니다.")
        return
    print(f"📬 확인된 지원 완료 메일 수: {len(applied_jobs)}")
    records = update_html(applied_jobs)
    save_status_json(records)
    print(f"✅ 상태 업데이트 완료: {len(records)}건")


if __name__ == "__main__":
    main()

# update_from_mail.py
import re, csv
from datetime import datetime
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

SCOPES = ['https://www.googleapis.com/auth/gmail.readonly']
TOKEN_PATH = r"c:/Users/pkill/Desktop/recruit_crawler-master/recruit_crawler-master/token.json"

def check_and_update_csv(csv_path):
    creds = Credentials.from_authorized_user_file(TOKEN_PATH, SCOPES)
    service = build('gmail', 'v1', credentials=creds)

    results = service.users().messages().list(
        userId='me',
        q='subject:(입사지원 완료) OR subject:(지원이 완료되었습니다)'
    ).execute()
    messages = results.get('messages', [])

    if not messages:
        print("📭 새 지원완료 메일 없음.")
        return

    rows = []
    updated = False

    with open(csv_path, newline='', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames
        if "status" not in fieldnames:
            fieldnames += ["status", "applied_at"]
        rows = list(reader)

    for m in messages:
        msg = service.users().messages().get(userId='me', id=m['id']).execute()
        subject = ""
        for h in msg['payload']['headers']:
            if h['name'] == 'Subject':
                subject = h['value']
        match = re.search(r"\[사람인\]\s*(.+?)에\s*입사지원", subject)
        if not match:
            continue
        company = match.group(1).strip()
        print(f"📨 지원 완료 메일 감지: {company}")

        for row in rows:
            if company in row["company"]:
                row["status"] = "applied"
                row["applied_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                updated = True

    if updated:
        with open(csv_path, 'w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=rows[0].keys())
            writer.writeheader()
            writer.writerows(rows)
        print("✅ CSV 상태 업데이트 완료")
    else:
        print("⚠️ 일치하는 회사 없음")

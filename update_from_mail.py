# update_from_mail_debug.py
import re, csv
from datetime import datetime
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

SCOPES = ['https://www.googleapis.com/auth/gmail.readonly']
TOKEN_PATH = r"c:/Users/pkill/Desktop/recruit_crawler-master/recruit_crawler-master/token.json"

def check_and_update_csv(csv_path):
    creds = Credentials.from_authorized_user_file(TOKEN_PATH, SCOPES)
    service = build('gmail', 'v1', credentials=creds)

    # Gmail 검색 쿼리
    query = '(subject:"입사지원 완료" OR subject:"지원이 완료되었습니다" OR subject:"성공적으로 완료되었습니다")'
    results = service.users().messages().list(
        userId='me',
        q=query,
        maxResults=10
    ).execute()

    messages = results.get('messages', [])
    if not messages:
        print("📭 새 지원완료 메일 없음.")
        return

    # 최근 5개 제목 출력 (디버그용)
    print("📋 최근 메일 제목:")
    for m in messages[:5]:
        msg = service.users().messages().get(userId='me', id=m['id']).execute()
        subject = next((h['value'] for h in msg['payload']['headers'] if h['name'] == 'Subject'), "")
        print("   →", subject)

    # CSV 로드
    rows = []
    updated = False
    with open(csv_path, newline='', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames
        if "status" not in fieldnames:
            fieldnames += ["status", "applied_at"]
        rows = list(reader)

    # 정규식 패턴 강화
    for m in messages:
        msg = service.users().messages().get(userId='me', id=m['id']).execute()
        subject = next((h['value'] for h in msg['payload']['headers'] if h['name'] == 'Subject'), "")
        
        # 예시: [사람인] (주)애니아이티에 입사지원이 성공적으로 완료되었습니다.
        match = re.search(r"\[사람인\]\s*(.+?)에\s*입사지원이\s*(?:성공적으로\s*)?완료", subject)
        if not match:
            continue
        company = match.group(1).strip()
        print(f"📨 지원 완료 메일 감지: {company}")

        for row in rows:
            if company in row["company"]:
                row["status"] = "applied"
                row["applied_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                updated = True

    # 저장
    if updated:
        with open(csv_path, 'w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=rows[0].keys())
            writer.writeheader()
            writer.writerows(rows)
        print("✅ CSV 상태 업데이트 완료")
    else:
        print("⚠️ 일치하는 회사 없음")


if __name__ == "__main__":
    check_and_update_csv("saramin_results_20251110_000000.csv")  # 또는 최신 CSV 경로로 수정

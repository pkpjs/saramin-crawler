from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
import base64, email, csv, datetime

def check_support_emails():
    creds = Credentials.from_authorized_user_file('token.json', ['https://www.googleapis.com/auth/gmail.readonly'])
    service = build('gmail', 'v1', credentials=creds)

    results = service.users().messages().list(
        userId='me',
        q='subject:(지원 OR 완료 OR 입사지원)'
    ).execute()

    messages = results.get('messages', [])
    applied_list = []

    for msg in messages:
        message = service.users().messages().get(userId='me', id=msg['id']).execute()
        msg_data = message['payload']['headers']

        subject = next(h['value'] for h in msg_data if h['name'] == 'Subject')

        # body
        msg_raw = message['payload']['parts'][0]['body']['data']
        body = base64.urlsafe_b64decode(msg_raw).decode()

        # 단순 정규식 예시: 회사명과 공고명 뽑기(추후 튜닝 예정)
        for line in body.split('\n'):
            if "회사명" in line or "기업명" in line:
                company = line.split(':')[1].strip()
            if "공고명" in line or "채용공고" in line:
                title = line.split(':')[1].strip()

        applied_list.append((company, title))

    return applied_list

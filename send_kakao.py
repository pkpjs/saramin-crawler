import requests
import json
import os

# ✅ GitHub Secrets에서 불러오기
REST_API_KEY = os.getenv("KAKAO_REST_API_KEY")
REDIRECT_URI = os.getenv("KAKAO_REDIRECT_URI")
REFRESH_TOKEN = os.getenv("KAKAO_REFRESH_TOKEN")
ACCESS_TOKEN = os.getenv("KAKAO_ACCESS_TOKEN")
PAGES_URL = os.getenv("PAGES_URL")

# ✅ 템플릿 ID
TEMPLATE_ID = 125299

# ✅ 토큰 갱신 함수
def refresh_access_token():
    url = "https://kauth.kakao.com/oauth/token"
    data = {
        "grant_type": "refresh_token",
        "client_id": REST_API_KEY,
        "refresh_token": REFRESH_TOKEN
    }
    response = requests.post(url, data=data)
    result = response.json()
    
    if "access_token" in result:
        return result["access_token"]
    else:
        print("[ERR] 토큰 갱신 실패:", result)
        raise Exception("토큰 갱신 실패")

# ✅ TOP10 데이터를 불러오는 함수 (예: HTML에서 파싱하거나 test.py에서 json으로 저장해둔 파일을 읽는 방식)
def load_top10():
    # 예시 데이터 (실제 test.py 결과와 연동하도록 향후 수정)
    return [
        {
            "title": "(주)시스메이트",
            "location": "대전 유성구",
            "job": "네트워크/보안 엔지니어",
            "url": "https://example.com/job1"
        },
        {
            "title": "(주)레이저발테크놀러지",
            "location": "경기 수원시 권선구",
            "job": "보안 솔루션 개발자",
            "url": "https://example.com/job2"
        }
        # TODO: 실제 TOP10 데이터를 test.py와 연결하여 자동 생성 (최대 10개)
    ]

def send_kakao_message():
    global ACCESS_TOKEN

    if not ACCESS_TOKEN:
        ACCESS_TOKEN = refresh_access_token()

    headers = {
        "Authorization": f"Bearer {ACCESS_TOKEN}",
        "Content-Type": "application/x-www-form-urlencoded"
    }

    top10_list = load_top10()

    template_args = {
        "jobs": json.dumps(top10_list, ensure_ascii=False),
        "total_count": str(len(top10_list)),
        "pages_url": PAGES_URL
    }

    url = "https://kapi.kakao.com/v2/api/talk/memo/send"
    data = {
        "template_id": TEMPLATE_ID,
        "template_args": json.dumps(template_args, ensure_ascii=False)
    }

    response = requests.post(url, headers=headers, data=data)
    result = response.json()
    print("카카오 응답:", result)

    if "result_code" in result and result["result_code"] == 0:
        print("✅ 메시지 전송 성공!")
    else:
        print("❌ 메시지 전송 실패")

if __name__ == "__main__":
    send_kakao_message()

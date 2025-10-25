import os
import requests
import json

REST_API_KEY = os.getenv("KAKAO_REST_API_KEY")
REFRESH_TOKEN = os.getenv("KAKAO_REFRESH_TOKEN")
ACCESS_TOKEN  = os.getenv("KAKAO_ACCESS_TOKEN")
PAGES_URL     = os.getenv("PAGES_URL")

def refresh_access_token():
    url = "https://kauth.kakao.com/oauth/token"
    data = {
        "grant_type": "refresh_token",
        "client_id": REST_API_KEY,
        "refresh_token": REFRESH_TOKEN
    }
    res = requests.post(url, data=data)
    res_json = res.json()
    if "access_token" in res_json:
        return res_json["access_token"]
    else:
        raise Exception("토큰 갱신 실패:", res_json)

def load_top10():
    # TODO: 실제 HTML에서 파싱하도록 연결/지금은 예시
    return [
        {"title": "(주)시스메이트", "location": "대전 유성구", "job": "보안 엔지니어", "url": "https://example.com/1"},
        {"title": "(주)레이저발테크놀러지", "location": "수원시 권선구", "job": "솔루션 개발자", "url": "https://example.com/2"}
    ]

def send_kakao_card():
    global ACCESS_TOKEN

    if not ACCESS_TOKEN:
        ACCESS_TOKEN = refresh_access_token()

    top10 = load_top10()

    contents = []
    for item in top10[:10]:
        contents.append({
            "title": item["title"],
            "description": f"{item['location']} · {item['job']}",
            "link": {
                "web_url": item["url"],
                "mobile_web_url": item["url"]
            }
        })

    template_object = {
        "object_type": "list",
        "header_title": "오늘의 TOP 채용 10선",
        "header_link": {"web_url": PAGES_URL, "mobile_web_url": PAGES_URL},
        "contents": contents,
        "buttons": [
            {
                "title": "전체 공고 보기",
                "link": {"web_url": PAGES_URL, "mobile_web_url": PAGES_URL}
            }
        ]
    }

    data = {"template_object": json.dumps(template_object, ensure_ascii=False)}
    headers = {"Authorization": f"Bearer {ACCESS_TOKEN}"}

    res = requests.post("https://kapi.kakao.com/v2/api/talk/memo/send", headers=headers, data=data)
    result = res.json()
    print("카카오 응답:", result)

    if result.get("result_code") == 0:
        print("✅ 메시지 전송 성공")
    else:
        print("❌ 메시지 전송 실패:", result)

if __name__ == "__main__":
    send_kakao_card()

import os
import requests
import json
from bs4 import BeautifulSoup
import re

# ✅ 환경 변수 (GitHub Secrets에서 불러오기)
REST_API_KEY = os.getenv("KAKAO_REST_API_KEY")
REFRESH_TOKEN = os.getenv("KAKAO_REFRESH_TOKEN")
ACCESS_TOKEN  = os.getenv("KAKAO_ACCESS_TOKEN")
PAGES_URL     = os.getenv("PAGES_URL", "https://pkpjs.github.io/test/saramin_results_latest.html")
HTML_PATH     = "docs/saramin_results_latest.html"

# ✅ 토큰 갱신 함수
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

# ✅ HTML 파싱 함수 (자동 구조 감지)
def extract_top10_from_html():
    with open(HTML_PATH, "r", encoding="utf-8", errors="ignore") as f:
        soup = BeautifulSoup(f.read(), "lxml")

    jobs = []

    # 🔹 1. 테이블 기반 추출
    for tr in soup.find_all("tr"):
        cols = tr.find_all(["td", "th"])
        if len(cols) >= 2:
            title_tag = tr.find("a", href=True)
            if title_tag:
                title = title_tag.get_text(strip=True)
                url = title_tag["href"]
                desc = " · ".join([c.get_text(strip=True) for c in cols[1:3]])
                jobs.append({"title": title, "desc": desc, "url": url})

    # 🔹 2. div/card 기반 파싱 보완
    if len(jobs) < 5:
        for a in soup.find_all("a", href=True):
            href = a["href"]
            title = a.get_text(strip=True)
            if "saramin.co.kr" in href and title:
                parent = a.find_parent()
                desc = parent.get_text(" ", strip=True) if parent else ""
                jobs.append({"title": title, "desc": desc, "url": href})

    # 🔹 중복 제거 + 상위 10개 선택
    unique = []
    seen = set()
    for job in jobs:
        key = (job["title"], job["url"])
        if key not in seen:
            seen.add(key)
            unique.append(job)
    return unique[:10]

# ✅ 카카오 메시지 전송 함수
def send_kakao_card(top10):
    global ACCESS_TOKEN

    if not ACCESS_TOKEN:
        ACCESS_TOKEN = refresh_access_token()

    headers = {"Authorization": f"Bearer {ACCESS_TOKEN}"}

    contents = []
    for item in top10:
        contents.append({
            "title": item["title"],
            "description": item["desc"] or "채용 공고",
            "link": {
                "web_url": item["url"],
                "mobile_web_url": item["url"]
            }
        })

    template_object = {
        "object_type": "list",
        "header_title": f"오늘의 TOP {len(contents)} 채용",
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
    res = requests.post("https://kapi.kakao.com/v2/api/talk/memo/send", headers=headers, data=data)
    result = res.json()
    print("카카오 응답:", result)

    if result.get("result_code") == 0:
        print("✅ 전송 성공!")
    else:
        print("❌ 전송 실패:", result)

# ✅ 메인 실행 흐름
if __name__ == "__main__":
    try:
        top10 = extract_top10_from_html()
        if not top10:
            print("⚠ TOP10 데이터를 찾지 못함. 전체 링크만 전송합니다.")
            top10 = [{"title": "채용 공고 확인", "desc": "전체보기", "url": PAGES_URL}]
        
        send_kakao_card(top10)
    except Exception as e:
        print("❌ 오류 발생:", e)

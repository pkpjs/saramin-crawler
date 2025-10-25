# -*- coding: utf-8 -*-
import os, json, requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))

REST_API_KEY  = os.getenv("KAKAO_REST_API_KEY")
REFRESH_TOKEN = os.getenv("KAKAO_REFRESH_TOKEN")
PAGES_URL     = os.getenv("PAGES_URL", "https://pkpjs.github.io/test/saramin_results_latest.html")
HTML_PATH     = "docs/saramin_results_latest.html"

SARAMIN_BASE = "https://www.saramin.co.kr"

def refresh_access_token() -> str:
    """Refresh Token -> Access Token"""
    url = "https://kauth.kakao.com/oauth/token"
    data = {
        "grant_type": "refresh_token",
        "client_id": REST_API_KEY,
        "refresh_token": REFRESH_TOKEN,
    }
    r = requests.post(url, data=data, timeout=20)
    r.raise_for_status()
    j = r.json()
    if "access_token" not in j:
        raise RuntimeError(f"Access token refresh failed: {j}")
    return j["access_token"]

def load_html_text() -> str:
    """로컬 HTML 우선, 없으면 Pages에서 가져오기"""
    try:
        with open(HTML_PATH, "r", encoding="utf-8", errors="ignore") as f:
            return f.read()
    except FileNotFoundError:
        resp = requests.get(PAGES_URL, timeout=20)
        resp.raise_for_status()
        return resp.text

def extract_top10():
    """
    HTML 표(DataFrame.to_html 유사)에서 상위 10개 추출
    - 컬럼: 제목/회사/위치/직무(있으면) 탐색
    - 제목 칸의 <a href> 추출(상대경로 -> 절대경로 보정)
    """
    html = load_html_text()
    soup = BeautifulSoup(html, "lxml")

    table = soup.find("table")
    if not table:
        return [], 0

    # 헤더
    headers = [th.get_text(strip=True) for th in table.select("thead tr th")]
    if not headers:
        first_tr = table.find("tr")
        if first_tr:
            headers = [th.get_text(strip=True) for th in first_tr.find_all(["th", "td"])]

    # 행
    rows = table.select("tbody tr")
    if not rows:
        rows = table.find_all("tr")[1:]  # 첫 tr 헤더 가정

    total = len(rows)

    # 컬럼 인덱스 헬퍼
    def idx(*names):
        for n in names:
            if n in headers:
                return headers.index(n)
        return None

    i_title   = idx("제목", "title")
    i_company = idx("회사", "company")
    i_loc     = idx("위치", "location")
    i_job     = idx("직무", "job")

    items = []
    for tr in rows[:10]:
        tds = tr.find_all("td")
        if not tds:
            continue

        # 제목/URL
        title = ""
        url = ""
        if i_title is not None and i_title < len(tds):
            title_cell = tds[i_title]
            a = title_cell.find("a", href=True)
            if a:
                title = a.get_text(strip=True)
                href = a["href"].strip()
                # 절대/상대 경로 모두 처리
                if href.startswith("http://") or href.startswith("https://"):
                    url = href
                else:
                    url = urljoin(SARAMIN_BASE, href)
            else:
                title = title_cell.get_text(strip=True)

        company = tds[i_company].get_text(strip=True) if i_company is not None and i_company < len(tds) else ""
        location = tds[i_loc].get_text(strip=True) if i_loc is not None and i_loc < len(tds) else ""
        job = tds[i_job].get_text(strip=True) if i_job is not None and i_job < len(tds) else ""

        # 카드 설명
        desc = " | ".join([t for t in [location, job] if t])

        # URL 없으면 전체보기로 폴백
        if not url:
            url = PAGES_URL

        # 제목/회사 비어있을 때 대비
        title = title or "채용공고"
        company = company or ""

        items.append({
            "title": title,
            "company": company,
            "desc": desc,
            "url": url
        })

    return items[:10], total

def send_list_card(access_token: str, header_title: str, contents: list, footer_button_title: str = "전체 공고 보기"):
    """
    카카오 기본 list 카드 전송(템플릿ID 불필요)
    contents: [{title, desc, url}]
    주의: 카카오 기본 list는 항목 수 제한이 있을 수 있으므로 5개 내외로 배치 권장
    """
    template_object = {
        "object_type": "list",
        "header_title": header_title,
        "header_link": {
            "web_url": PAGES_URL,
            "mobile_web_url": PAGES_URL
        },
        "contents": [
            {
                "title": c["title"] if c.get("company") == "" else f"{c['title']}",
                "description": (f"{c.get('company', '')} · {c.get('desc', '')}").strip(" ·"),
                "link": {
                    "web_url": c["url"],
                    "mobile_web_url": c["url"]
                }
            }
            for c in contents
        ],
        "buttons": [
            {
                "title": footer_button_title,
                "link": {"web_url": PAGES_URL, "mobile_web_url": PAGES_URL}
            }
        ]
    }

    url = "https://kapi.kakao.com/v2/api/talk/memo/default/send"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
    }
    data = {"template_object": json.dumps(template_object, ensure_ascii=False)}
    r = requests.post(url, headers=headers, data=data, timeout=20)
    try:
        js = r.json()
    except Exception:
        raise RuntimeError(f"Kakao send error: {r.status_code} {r.text}")

    if js.get("result_code") != 0:
        raise RuntimeError(f"Kakao send failed: {js}")
    return js

def chunk(lst, size):
    for i in range(0, len(lst), size):
        yield lst[i:i+size]

def main():
    access_token = refresh_access_token()

    items, total = extract_top10()
    today = datetime.now(KST).strftime("%Y-%m-%d")
    if not items:
        # 아무 것도 못 뽑으면 안내 텍스트만
        template_object = {
            "object_type": "text",
            "text": f"[{today}] 채용 데이터를 불러오지 못했어요.\n아래 버튼으로 전체 목록을 확인하세요.",
            "link": {"web_url": PAGES_URL, "mobile_web_url": PAGES_URL},
            "button_title": "전체 공고 보기"
        }
        url = "https://kapi.kakao.com/v2/api/talk/memo/default/send"
        headers = {"Authorization": f"Bearer {access_token}"}
        data = {"template_object": json.dumps(template_object, ensure_ascii=False)}
        r = requests.post(url, headers=headers, data=data, timeout=20)
        print("Fallback sent:", r.text)
        return

    # 최대 10개 → 5개씩 2회 전송(캐러셀 느낌)
    batches = list(chunk(items, 5))
    for idx, batch in enumerate(batches, start=1):
        header = f"{today} 채용공고 TOP {len(items)} (#{(idx-1)*5+1}–#{(idx-1)*5+len(batch)})"
        send_list_card(access_token, header, batch)

    print("✅ 전송 완료: list 카드 {}건(총 {}개 항목)".format(len(batches), len(items)))

if __name__ == "__main__":
    main()

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
SARAMIN_BASE  = "https://www.saramin.co.kr"

# ✅ Token Refresh
def refresh_access_token() -> str:
    url = "https://kauth.kakao.com/oauth/token"
    data = {
        "grant_type": "refresh_token",
        "client_id": REST_API_KEY,
        "refresh_token": REFRESH_TOKEN,
    }
    r = requests.post(url, data=data, timeout=20)
    r.raise_for_status()
    js = r.json()
    if "access_token" not in js:
        raise RuntimeError(f"Access token refresh failed: {js}")
    return js["access_token"]

# ✅ HTML Load
def load_html_text() -> str:
    try:
        with open(HTML_PATH, "r", encoding="utf-8", errors="ignore") as f:
            return f.read()
    except FileNotFoundError:
        r = requests.get(PAGES_URL, timeout=20)
        r.raise_for_status()
        return r.text

# ✅ Top10 추출
def extract_top10():
    html = load_html_text()
    soup = BeautifulSoup(html, "lxml")

    table = soup.find("table")
    if not table:
        return [], 0

    headers = [th.get_text(strip=True) for th in table.select("thead tr th")]
    if not headers:
        first = table.find("tr")
        if first:
            headers = [th.get_text(strip=True) for th in first.find_all(["th", "td"])]

    rows = table.select("tbody tr") or table.find_all("tr")[1:]
    total = len(rows)

    def idx(*names):
        for n in names:
            if n in headers:
                return headers.index(n)
        return None

    i_link = idx("링크", "제목")
    i_company = idx("회사", "company")
    i_loc = idx("위치", "location")
    i_job = idx("직무", "job")
    i_direct = idx("바로가기")

    items = []
    for tr in rows[:10]:
        tds = tr.find_all("td")
        if not tds:
            continue

        title, url = "", ""
        if i_link is not None and i_link < len(tds):
            a = tds[i_link].find("a", href=True)
            if a:
                title = a.get_text(strip=True)
                href = a["href"].strip()
                url = href if href.startswith("http") else urljoin(SARAMIN_BASE, href)
            else:
                title = tds[i_link].get_text(strip=True)

        # "바로가기" 백업
        if not url and i_direct is not None and i_direct < len(tds):
            a2 = tds[i_direct].find("a", href=True)
            if a2:
                href2 = a2["href"].strip()
                url = href2 if href2.startswith("http") else urljoin(SARAMIN_BASE, href2)

        company = tds[i_company].get_text(strip=True) if i_company is not None and i_company < len(tds) else ""
        location = tds[i_loc].get_text(strip=True) if i_loc is not None and i_loc < len(tds) else ""
        job = tds[i_job].get_text(strip=True) if i_job is not None and i_job < len(tds) else ""

        desc = " | ".join([t for t in [location, job] if t])
        if not url:
            url = PAGES_URL

        items.append({
            "title": title or "(제목 없음)",
            "company": company,
            "desc": desc,
            "url": url
        })

    return items[:10], total

# ✅ 카카오 list 카드 전송
def send_list_card(access_token: str, header_title: str, contents: list):
    template_object = {
        "object_type": "list",
        "header_title": header_title,
        "header_link": {"web_url": PAGES_URL, "mobile_web_url": PAGES_URL},
        "contents": [
            {
                "title": c["title"],
                "description": (f"{c['company']} · {c['desc']}").strip(" ·"),
                "link": {"web_url": c["url"], "mobile_web_url": c["url"]}
            }
            for c in contents
        ],
        "buttons": [
            {
                "title": "전체 공고 보기",
                "link": {"web_url": PAGES_URL, "mobile_web_url": PAGES_URL}
            }
        ]
    }

    r = requests.post(
        "https://kapi.kakao.com/v2/api/talk/memo/default/send",
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/x-www-form-urlencoded;charset=utf-8",
        },
        data={"template_object": json.dumps(template_object, ensure_ascii=False)},
        timeout=20,
    )
    js = r.json()
    print("📩 카카오 응답:", js)
    if js.get("result_code") != 0:
        raise RuntimeError(f"전송 실패: {js}")
    return js

def chunk(lst, size):
    for i in range(0, len(lst), size):
        yield lst[i:i+size]

def main():
    access_token = refresh_access_token()
    items, total = extract_top10()
    today = datetime.now(KST).strftime("%Y-%m-%d")

    if not items:
        print("⚠ 데이터 없음")
        return

    batches = list(chunk(items, 5))
    for idx, batch in enumerate(batches, start=1):
        header = f"{today} 채용공고 TOP 10 ({(idx-1)*5+1}-{(idx-1)*5+len(batch)})"
        send_list_card(access_token, header, batch)

    print("✅ 전송 완료! 총 {}개 항목 ({}회 전송)".format(len(items), len(batches)))

if __name__ == "__main__":
    main()

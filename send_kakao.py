# -*- coding: utf-8 -*-
import os, json, re, requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin
from datetime import datetime, timedelta, timezone

# ====== 설정 ======
KST = timezone(timedelta(hours=9))
REST_API_KEY  = os.getenv("KAKAO_REST_API_KEY")
REFRESH_TOKEN = os.getenv("KAKAO_REFRESH_TOKEN")
PAGES_URL     = os.getenv("PAGES_URL", "https://pkpjs.github.io/test/saramin_results_latest.html")
HTML_PATH     = "docs/saramin_results_latest.html"
SARAMIN_BASE  = "https://www.saramin.co.kr"

# IT/보안 필터 키워드 (제목/직무/회사/위치에 하나라도 포함되면 통과)
IT_SECURITY_KEYWORDS = ["보안", "시스템", "네트워크", "정보", "백엔드", "서버", "IT"]

# 마감일 임박 임계 (KST 기준 N일 이내면 임박 표시)
IMMINENT_DAYS = 7

# ====== 공통 함수 ======
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
        raise RuntimeError(f"토큰 갱신 실패: {js}")
    return js["access_token"]

def load_html_text() -> str:
    # 로컬 우선, 없으면 Pages에서
    try:
        with open(HTML_PATH, "r", encoding="utf-8", errors="ignore") as f:
            return f.read()
    except FileNotFoundError:
        r = requests.get(PAGES_URL, timeout=20)
        r.raise_for_status()
        return r.text

def parse_deadline(text: str) -> datetime | None:
    """
    사람인 마감 표기 예: "~ 11/14(금)", "~11.02", "~ 11-04", "채용시 마감", "상시"
    - 월/일만 있으면 올해 기준으로 해석
    - 날짜가 이미 지나도 '올해' 기준 유지 (단, 너무 과거면 +1년 보정)
    """
    if not text:
        return None
    t = text.strip()
    if any(k in t for k in ["상시", "채용시", "수시"]):
        return None  # 사실상 무기한 → 정렬에서 뒤로 밀림

    # "~ 11/14(금)" 등에서 월/일 추출
    m = re.search(r'(\d{1,2})\s*[./-]\s*(\d{1,2})', t)
    if not m:
        # "11월 14일" 패턴
        m = re.search(r'(\d{1,2})\s*월\s*(\d{1,2})\s*일', t)
    if not m:
        return None

    month = int(m.group(1))
    day   = int(m.group(2))
    now = datetime.now(KST)
    year = now.year

    try_date = datetime(year, month, day, tzinfo=KST)
    # 너무 과거처럼 보이면 +1년 (연말/연초 걸림 방지)
    if try_date < now - timedelta(days=180):
        try_date = datetime(year + 1, month, day, tzinfo=KST)
    return try_date

def is_it_security(hit_fields: list[str]) -> bool:
    bag = " ".join([x for x in hit_fields if x]).lower()
    return any(k.lower() in bag for k in IT_SECURITY_KEYWORDS)

# ====== 파싱 ======
def extract_items():
    """
    표 컬럼 예:
    링크(제목) | 회사 | 위치 | 경력 | 학력 | 마감일 | 바로가기
    """
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

    i_link   = idx("링크", "제목")
    i_company= idx("회사", "company")
    i_loc    = idx("위치", "location")
    i_job    = idx("직무", "job")
    i_dead   = idx("마감일", "마감", "deadline")
    i_direct = idx("바로가기")

    items = []
    for tr in rows:
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

        # 보조 URL: '바로가기' 칼럼
        if not url and i_direct is not None and i_direct < len(tds):
            a2 = tds[i_direct].find("a", href=True)
            if a2:
                href2 = a2["href"].strip()
                url = href2 if href2.startswith("http") else urljoin(SARAMIN_BASE, href2)

        company = tds[i_company].get_text(strip=True) if i_company is not None and i_company < len(tds) else ""
        loc     = tds[i_loc].get_text(strip=True)     if i_loc     is not None and i_loc     < len(tds) else ""
        job     = tds[i_job].get_text(strip=True)     if i_job     is not None and i_job     < len(tds) else ""
        deadraw = tds[i_dead].get_text(strip=True)    if i_dead    is not None and i_dead    < len(tds) else ""

        deadline = parse_deadline(deadraw)
        items.append({
            "title": title or "(제목 없음)",
            "company": company,
            "location": loc,
            "job": job,
            "deadline": deadline,   # datetime | None
            "deadline_text": deadraw,
            "url": url or PAGES_URL
        })
    return items, total

# ====== 정렬/필터/상위 N ======
def select_top10(items):
    # 1) IT/보안 키워드 필터
    filtered = [x for x in items if is_it_security([x["title"], x["company"], x["location"], x["job"]])]

    # 2) 마감일 임박순 정렬 (None은 뒤로)
    def sort_key(x):
        return (x["deadline"] is None, x["deadline"] or datetime.max.replace(tzinfo=KST))
    filtered.sort(key=sort_key)

    # 3) 부족하면 일반 공고로 보충 (같은 정렬)
    if len(filtered) < 10:
        others = [x for x in items if x not in filtered]
        others.sort(key=sort_key)
        filtered.extend(others)

    # 4) TOP10
    return filtered[:10]

# ====== 전송 ======
def send_text_header(access_token: str, total_count: int):
    today = datetime.now(KST).strftime("%Y-%m-%d")
    text = f"📅 {today} 채용공고 TOP 10 (보안/IT 직군)\n총 {total_count}건에서 선별·정렬했습니다."
    url = "https://kapi.kakao.com/v2/api/talk/memo/default/send"
    template_object = {
        "object_type": "text",
        "text": text,
        "link": {"web_url": PAGES_URL, "mobile_web_url": PAGES_URL},
        "button_title": "전체 공고 보기"
    }
    headers = {"Authorization": f"Bearer {access_token}"}
    data = {"template_object": json.dumps(template_object, ensure_ascii=False)}
    r = requests.post(url, headers=headers, data=data, timeout=20)
    r.raise_for_status()
    js = r.json()
    if js.get("result_code") != 0:
        raise RuntimeError(f"헤더 전송 실패: {js}")

def send_feed_card(access_token: str, rank: int, item: dict):
    # 마감일 텍스트 구성
    now = datetime.now(KST)
    imminent = (item["deadline"] and (item["deadline"].date() - now.date()).days <= IMMINENT_DAYS)
    dead_txt = item["deadline_text"] or ("상시" if item["deadline"] is None else "")
    prefix = "🔥 " if imminent else ""
    desc_lines = []
    if item["company"]:
        desc_lines.append(item["company"])
    if item["location"] or item["job"]:
        desc_lines.append(" | ".join([t for t in [item["location"], item["job"]] if t]))
    if dead_txt:
        desc_lines.append(f"마감일: {dead_txt}")
    desc = "\n".join(desc_lines)

    template_object = {
        "object_type": "feed",
        "content": {
            "title": f"{prefix}{rank}위 | {item['title']}",
            "description": desc,
            "link": {"web_url": item["url"], "mobile_web_url": item["url"]},
        },
        "buttons": [
            {"title": "공고 바로가기 🔗", "link": {"web_url": item["url"], "mobile_web_url": item["url"]}}
        ]
    }
    url = "https://kapi.kakao.com/v2/api/talk/memo/default/send"
    headers = {"Authorization": f"Bearer {access_token}"}
    data = {"template_object": json.dumps(template_object, ensure_ascii=False)}
    r = requests.post(url, headers=headers, data=data, timeout=20)
    r.raise_for_status()
    js = r.json()
    if js.get("result_code") != 0:
        raise RuntimeError(f"{rank}위 전송 실패: {js}")

def send_full_button(access_token: str):
    template_object = {
        "object_type": "feed",
        "content": {
            "title": "전체 공고 한 번에 보기",
            "description": "GitHub Pages에서 최신 전체 목록 확인",
            "link": {"web_url": PAGES_URL, "mobile_web_url": PAGES_URL},
        },
        "buttons": [
            {"title": "전체보기 🔗", "link": {"web_url": PAGES_URL, "mobile_web_url": PAGES_URL}}
        ]
    }
    url = "https://kapi.kakao.com/v2/api/talk/memo/default/send"
    headers = {"Authorization": f"Bearer {access_token}"}
    data = {"template_object": json.dumps(template_object, ensure_ascii=False)}
    r = requests.post(url, headers=headers, data=data, timeout=20)
    r.raise_for_status()

# ====== 메인 ======
def main():
    access_token = refresh_access_token()
    items_all, total = extract_items()
    if not items_all:
        print("❌ 데이터 추출 실패(표 미발견)")
        return

    top10 = select_top10(items_all)

    # 헤더(날짜/요약)
    send_text_header(access_token, total_count=total)

    # TOP10 개별 카드
    for i, it in enumerate(top10, start=1):
        send_feed_card(access_token, i, it)

    # 전체보기 버튼
    send_full_button(access_token)

    print(f"✅ 전송 완료: 총 {len(top10)}개 (전체 원본 {total}건)")

if __name__ == "__main__":
    main()
